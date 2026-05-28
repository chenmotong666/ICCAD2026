module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n12,
    n11,
    n6,
    n7,
    n8,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output [3:0] n12;
  output n11;
  output n6;
  output n7;
  output n8;
  output n9;
  output n10;

  wire \$and$testcase/test91/test91.v:20$4_Y , \$and$testcase/test91/test91.v:31$17_Y , \$auto$rtlil.cc:3255:Not$36 , n13, n14, n15, n16, n17, n18, n19, n30, n31, n40, n41, n42, n43, n44, n45, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test91/test91.v:51$28  (n80, n2[0], n2[1]);
  and   \$and$testcase/test91/test91.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test91/test91.v:25$11  (n30, n2[1], n3[1]);
  and   \$and$testcase/test91/test91.v:28$14  (n40, n2[2], n3[2]);
  and   \$and$testcase/test91/test91.v:32$19  (n44, n2[2], n3[2]);
  xor   \$xor$testcase/test91/test91.v:30$16  (n42, n2[2], n3[2]);
  not   \$not$testcase/test91/test91.v:42$32  (n63, n4);
  and   \$and$testcase/test91/test91.v:31$17  (\$and$testcase/test91/test91.v:31$17_Y , n2[2], n5);
  or    \$or$testcase/test91/test91.v:29$15  (n41, n2[2], n5);
  or    \$or$testcase/test91/test91.v:33$20  (n45, n2[2], n5);
  or    \$or$testcase/test91/test91.v:52$29  (n81, n80, n3[0]);
  or    \$or$testcase/test91/test91.v:17$2  (n14, n13, n4);
  xor   \$xor$testcase/test91/test91.v:26$12  (n31, n30, n5);
  not   \$not$testcase/test91/test91.v:31$18  (n43, \$and$testcase/test91/test91.v:31$17_Y );
  or    \$or$testcase/test91/test91.v:34$21  (n56, n40, n41);
  not   \$not$testcase/test91/test91.v:53$34  (n82, n81);
  not   \$not$testcase/test91/test91.v:18$30  (n15, n14);
  or    \$or$testcase/test91/test91.v:27$13  (n7, n31, n4);
  or    \$or$testcase/test91/test91.v:35$22  (n57, n56, n42);
  or    \$or$testcase/test91/test91.v:19$3  (n16, n15, n5);
  or    \$or$testcase/test91/test91.v:36$23  (n8, n57, n43);
  and   \$and$testcase/test91/test91.v:20$4  (\$and$testcase/test91/test91.v:20$4_Y , n16, n2[1]);
  and   \$and$testcase/test91/test91.v:45$27  (n65, n31, n8);
  not   \$not$testcase/test91/test91.v:20$5  (n17, \$and$testcase/test91/test91.v:20$4_Y );
  dff g30 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  or    \$or$testcase/test91/test91.v:21$6  (\$auto$rtlil.cc:3255:Not$36 , n17, n3[1]);
  not   \$not$testcase/test91/test91.v:21$7  (n18, \$auto$rtlil.cc:3255:Not$36 );
  not   \$not$testcase/test91/test91.v:22$9  (n19, \$auto$rtlil.cc:3255:Not$36 );
  xor   \$xor$testcase/test91/test91.v:23$10  (n6, n19, n2[2]);

  assign n12[1] = n7;
  assign n12[2] = n8;
  assign n12[0] = n6;
  assign n9 = 1'b1;
  assign n10 = n4;
  assign n12[3] = 1'b1;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
