module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n7,
    n8,
    n6,
    n12,
    n11,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n7;
  output n8;
  output n6;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test88/test88.v:34$21_Y , \$and$testcase/test88/test88.v:38$26_Y , \$or$testcase/test88/test88.v:24$10_Y , \$xor$testcase/test88/test88.v:19$3_Y , \$xor$testcase/test88/test88.v:25$12_Y , n13, n15, n16, n17, n18, n20, n21, n22, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n56, n57, n63, n65, n80, n81, n82, renamed_signal;

  and   \$and$testcase/test88/test88.v:55$38  (n80, n2[0], n2[1]);
  not   \$not$testcase/test88/test88.v:23$9  (n20, n2[2]);
  and   renamed_gate (n13, n2[0], n3[0]);
  and   \$and$testcase/test88/test88.v:28$15  (n30, n2[1], n3[1]);
  and   \$and$testcase/test88/test88.v:31$18  (n40, n2[2], n3[2]);
  and   \$and$testcase/test88/test88.v:35$23  (n44, n2[2], n3[2]);
  and   \$and$testcase/test88/test88.v:39$28  (n48, n2[2], n3[2]);
  xor   \$xor$testcase/test88/test88.v:33$20  (n42, n2[2], n3[2]);
  xor   \$xor$testcase/test88/test88.v:37$25  (n46, n2[2], n3[2]);
  not   \$not$testcase/test88/test88.v:49$42  (n63, n4);
  and   \$and$testcase/test88/test88.v:34$21  (\$and$testcase/test88/test88.v:34$21_Y , n2[2], n5);
  and   \$and$testcase/test88/test88.v:38$26  (\$and$testcase/test88/test88.v:38$26_Y , n2[2], n5);
  or    \$or$testcase/test88/test88.v:32$19  (n41, n2[2], n5);
  or    \$or$testcase/test88/test88.v:36$24  (n45, n2[2], n5);
  or    \$or$testcase/test88/test88.v:40$29  (n49, n2[2], n5);
  or    \$or$testcase/test88/test88.v:56$39  (n81, n80, n3[0]);
  or    \$or$testcase/test88/test88.v:24$10  (\$or$testcase/test88/test88.v:24$10_Y , n20, n3[2]);
  or    \$or$testcase/test88/test88.v:17$2  (renamed_signal, n13, n4);
  xor   \$xor$testcase/test88/test88.v:29$16  (n31, n30, n5);
  not   \$not$testcase/test88/test88.v:34$22  (n43, \$and$testcase/test88/test88.v:34$21_Y );
  not   \$not$testcase/test88/test88.v:38$27  (n47, \$and$testcase/test88/test88.v:38$26_Y );
  or    \$or$testcase/test88/test88.v:41$30  (n56, n40, n41);
  not   \$not$testcase/test88/test88.v:57$44  (n82, n81);
  not   \$not$testcase/test88/test88.v:24$11  (n21, \$or$testcase/test88/test88.v:24$10_Y );
  not   \$not$testcase/test88/test88.v:18$40  (n15, renamed_signal);
  or    \$or$testcase/test88/test88.v:30$17  (n7, n31, n4);
  or    \$or$testcase/test88/test88.v:42$31  (n57, n56, n42);
  xor   \$xor$testcase/test88/test88.v:25$12  (\$xor$testcase/test88/test88.v:25$12_Y , n21, n5);
  xor   \$xor$testcase/test88/test88.v:19$3  (\$xor$testcase/test88/test88.v:19$3_Y , n15, n5);
  or    \$or$testcase/test88/test88.v:43$32  (n8, n57, n43);
  not   \$not$testcase/test88/test88.v:25$13  (n22, \$xor$testcase/test88/test88.v:25$12_Y );
  not   \$not$testcase/test88/test88.v:19$4  (n16, \$xor$testcase/test88/test88.v:19$3_Y );
  and   \$and$testcase/test88/test88.v:52$36  (n65, n31, n8);
  xor   \$xor$testcase/test88/test88.v:26$14  (n6, n22, n2[1]);
  xor   \$xor$testcase/test88/test88.v:20$5  (n17, n16, n2[1]);
  dff g37 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  xor   \$xor$testcase/test88/test88.v:54$37  (n12, n6, n7);
  and   \$and$testcase/test88/test88.v:21$6  (n18, n17, n3[1]);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
