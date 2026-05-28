module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n11,
    n7,
    n8,
    n12,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n6;
  output n11;
  output n7;
  output n8;
  output n12;
  output n9;
  output n10;

  wire \$and$testcase/test93/test93.v:24$10_Y , \$and$testcase/test93/test93.v:33$20_Y , \$and$testcase/test93/test93.v:37$25_Y , \$or$testcase/test93/test93.v:19$3_Y , \$or$testcase/test93/test93.v:25$12_Y , \$xor$testcase/test93/test93.v:20$5_Y , \$xor$testcase/test93/test93.v:51$35_Y , __fo_n2_2__0__, __fo_n2_2__1__, __fo_n2_2__2__, __fo_n2_2__3__, __fo_n3_2__0__, __fo_n3_2__1__, __fo_n3_2__2__, __fo_n5_0__, __fo_n5_1__, __fo_n5_2__, __fo_n5_3__, n13, n14, n15, n16, n17, n18, n20, n21, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n56, n57, n63, n80, n81, n82;

  and   \$and$testcase/test93/test93.v:53$38  (n80, n2[0], n2[1]);
  buf   fo_buf_0 (__fo_n2_2__0__, n2[2]);
  buf   fo_buf_1 (__fo_n2_2__1__, n2[2]);
  buf   fo_buf_2 (__fo_n2_2__2__, n2[2]);
  buf   fo_buf_3 (__fo_n2_2__3__, n2[2]);
  and   \$and$testcase/test93/test93.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test93/test93.v:27$14  (n30, n2[1], n3[1]);
  buf   fo_buf_4 (__fo_n3_2__0__, n3[2]);
  buf   fo_buf_5 (__fo_n3_2__1__, n3[2]);
  buf   fo_buf_6 (__fo_n3_2__2__, n3[2]);
  not   \$not$testcase/test93/test93.v:48$42  (n63, n4);
  buf   fo_buf_7 (__fo_n5_0__, n5);
  buf   fo_buf_8 (__fo_n5_1__, n5);
  buf   fo_buf_9 (__fo_n5_2__, n5);
  buf   fo_buf_10 (__fo_n5_3__, n5);
  or    \$or$testcase/test93/test93.v:54$39  (n81, n80, n3[0]);
  or    \$or$testcase/test93/test93.v:17$2  (n14, n13, n4);
  and   \$and$testcase/test93/test93.v:30$17  (n40, __fo_n2_2__0__, __fo_n3_2__0__);
  and   \$and$testcase/test93/test93.v:34$22  (n44, __fo_n2_2__0__, __fo_n3_2__1__);
  and   \$and$testcase/test93/test93.v:38$27  (n48, __fo_n2_2__1__, __fo_n3_2__1__);
  xor   \$xor$testcase/test93/test93.v:32$19  (n42, __fo_n2_2__3__, __fo_n3_2__2__);
  xor   \$xor$testcase/test93/test93.v:36$24  (n46, __fo_n2_2__3__, __fo_n3_2__2__);
  and   \$and$testcase/test93/test93.v:33$20  (\$and$testcase/test93/test93.v:33$20_Y , __fo_n2_2__0__, __fo_n5_0__);
  and   \$and$testcase/test93/test93.v:37$25  (\$and$testcase/test93/test93.v:37$25_Y , __fo_n2_2__1__, __fo_n5_0__);
  or    \$or$testcase/test93/test93.v:31$18  (n41, __fo_n2_2__2__, __fo_n5_2__);
  or    \$or$testcase/test93/test93.v:35$23  (n45, __fo_n2_2__2__, __fo_n5_2__);
  or    \$or$testcase/test93/test93.v:39$28  (n49, __fo_n2_2__2__, __fo_n5_3__);
  xor   \$xor$testcase/test93/test93.v:28$15  (n31, n30, __fo_n5_3__);
  not   \$not$testcase/test93/test93.v:55$44  (n82, n81);
  not   \$not$testcase/test93/test93.v:18$40  (n15, n14);
  not   \$not$testcase/test93/test93.v:33$21  (n43, \$and$testcase/test93/test93.v:33$20_Y );
  not   \$not$testcase/test93/test93.v:37$26  (n47, \$and$testcase/test93/test93.v:37$25_Y );
  or    \$or$testcase/test93/test93.v:40$29  (n56, n40, n41);
  or    \$or$testcase/test93/test93.v:29$16  (n7, n31, n4);
  or    \$or$testcase/test93/test93.v:19$3  (\$or$testcase/test93/test93.v:19$3_Y , n15, __fo_n5_1__);
  or    \$or$testcase/test93/test93.v:41$30  (n57, n56, n42);
  not   \$not$testcase/test93/test93.v:19$4  (n16, \$or$testcase/test93/test93.v:19$3_Y );
  or    \$or$testcase/test93/test93.v:42$31  (n8, n57, n43);
  xor   \$xor$testcase/test93/test93.v:20$5  (\$xor$testcase/test93/test93.v:20$5_Y , n16, n2[1]);
  xor   \$xor$testcase/test93/test93.v:51$35  (\$xor$testcase/test93/test93.v:51$35_Y , n31, n8);
  not   \$not$testcase/test93/test93.v:20$6  (n17, \$xor$testcase/test93/test93.v:20$5_Y );
  not   \$not$testcase/test93/test93.v:51$36  (n11, \$xor$testcase/test93/test93.v:51$35_Y );
  xor   \$xor$testcase/test93/test93.v:21$7  (n18, n17, n3[1]);
  or    \$or$testcase/test93/test93.v:23$9  (n20, n18, __fo_n2_2__1__);
  and   \$and$testcase/test93/test93.v:24$10  (\$and$testcase/test93/test93.v:24$10_Y , n20, __fo_n3_2__0__);
  not   \$not$testcase/test93/test93.v:24$11  (n21, \$and$testcase/test93/test93.v:24$10_Y );
  or    \$or$testcase/test93/test93.v:25$12  (\$or$testcase/test93/test93.v:25$12_Y , n21, __fo_n5_1__);
  not   \$not$testcase/test93/test93.v:25$13  (n6, \$or$testcase/test93/test93.v:25$12_Y );
  xor   \$xor$testcase/test93/test93.v:52$37  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule
